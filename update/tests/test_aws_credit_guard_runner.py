"""Real caller identity, atomic launch and cost bounds, without network access."""
from copy import deepcopy
from datetime import timedelta
import base64,hashlib,json
import pytest
from tools import aws_credit_guard as guard
from tools import aws_credit_guard_runner as runner
from tools import aws_credit_guard_deploy as deploy
from tests.test_aws_credit_guard import config,NOW,resource_tags,instance

INFRA={'image_ids':['ami-EXAMPLE'],'subnet_id':'subnet-EXAMPLE','vpc_id':'vpc-EXAMPLE',
 'instance_role_arn':'arn:aws:iam::123456789012:role/EXAMPLE',
 'instance_profile_arn':'arn:aws:iam::123456789012:instance-profile/EXAMPLE'}


def request():
    script=b'echo EXAMPLE-bootstrap'
    return {'action':'launch','job':'a'*32,'image_id':'ami-EXAMPLE','instance_type':'g6.12xlarge','root_gib':200,
        'minutes':45,'budget_usd':'6','source_sha256':'b'*64,'script_b64':base64.b64encode(script).decode(),
        'script_sha256':hashlib.sha256(script).hexdigest()}


class Cloud:
    def __init__(self):
        self.calls=[];self.instances=[];self.volumes=[];self.lock=None;self.launch_error=False
        self.identity='arn:aws:sts::123456789012:assumed-role/'+config()['execution_role']+'/EXAMPLE'
        self.state={'schema':guard.SCHEMA,'guard_id':config()['guard_id'],'checked_at':NOW.isoformat(),
            'launch_lease_expires_at':(NOW+timedelta(seconds=180)).isoformat(),'blocked':False,'latched':False}
        self.credit='12.23'
    def factory(self,*args):return self
    def get_caller_identity(self):self.calls.append(('identity',{}));return {'Arn':self.identity}
    def get_parameter(self,**kw):return {'Parameter':{'Value':json.dumps(self.state)}}
    def get_account_plan_state(self):return {'accountId':config()['account_id'],'accountPlanStatus':'ACTIVE',
        'accountPlanRemainingCredits':{'unit':'USD','amount':self.credit}}
    def describe_instances(self,**kw):self.calls.append(('inventory',kw));return {'Reservations':[{'Instances':self.instances}]}
    def describe_volumes(self,**kw):return {'Volumes':self.volumes}
    def put_parameter(self,**kw):
        self.calls.append(('lock',kw))
        if self.lock is not None:raise RuntimeError('ParameterAlreadyExists')
        assert kw['Overwrite']is False and kw['Tier']=='Standard';self.lock=kw['Value'];return {}
    def create_security_group(self,**kw):self.calls.append(('create-group',kw));return {'GroupId':'sg-EXAMPLE'}
    def run_instances(self,**kw):
        self.calls.append(('launch',kw))
        if self.launch_error:raise TimeoutError('EXAMPLE_PRIVATE_DIAGNOSTIC')
        return {'Instances':[{'InstanceId':'i-0123456789abcdef0'}]}
    def terminate_instances(self,**kw):self.calls.append(('terminate',kw));return {}
    def send_command(self,**kw):self.calls.append(('command',kw));return {'Command':{'CommandId':'a'*8+'-'+'a'*4+'-'+'a'*4+'-'+'a'*4+'-'+'a'*12}}


def launch(cloud,req=None,cfg=None):return runner.launch(req or request(),cfg or config(),INFRA,factory=cloud.factory,now=NOW)


def test_only_expected_role_can_launch_a_single_encrypted_self_terminating_session():
    cloud=Cloud();result=launch(cloud)
    assert cloud.calls[0][0]=='identity'
    assert [c[0]for c in cloud.calls].index('lock')<[c[0]for c in cloud.calls].index('launch')
    args=next(v for k,v in cloud.calls if k=='launch')
    assert args['MinCount']==args['MaxCount']==1
    assert args['UserData'].startswith('#!/bin/bash\nset -euo pipefail\n/sbin/shutdown -h +45\n')
    assert args['InstanceInitiatedShutdownBehavior']=='terminate'
    assert args['MetadataOptions']['HttpPutResponseHopLimit']==1 and args['MetadataOptions']['HttpTokens']=='required'
    assert args['IamInstanceProfile']=={'Arn':INFRA['instance_profile_arn']}
    assert all(m['Ebs']['Encrypted']and m['Ebs']['DeleteOnTermination']for m in args['BlockDeviceMappings'])
    for spec in args['TagSpecifications']:
        tags={t['Key']:t['Value']for t in spec['Tags']}
        assert tags[guard.TAG]==config()['guard_id']and tags[guard.EPHEMERAL]=='true'and tags[guard.BUDGET]=='6'
    assert result['execution_role']==cloud.identity and 'script_b64'not in result


@pytest.mark.parametrize('identity',['arn:aws:iam::123456789012:root',
 'arn:aws:sts::999999999999:assumed-role/byul-credit-example-execution/EXAMPLE',
 'arn:aws:sts::123456789012:assumed-role/other/EXAMPLE'])
def test_root_foreign_account_and_other_role_fail_before_cloud_mutation(identity):
    cloud=Cloud();cloud.identity=identity
    with pytest.raises(ValueError,match='EXECUTION_ROLE_UNVERIFIED'):launch(cloud)
    assert [c[0]for c in cloud.calls]==['identity']


@pytest.mark.parametrize('damage',['stale','future','blocked','latched','expired','credit-low','missing-credit','wrong-guard'])
def test_no_new_session_when_credit_or_monitor_health_is_unverified(damage):
    cloud=Cloud()
    if damage=='stale':cloud.state['checked_at']=(NOW-timedelta(seconds=91)).isoformat()
    elif damage=='future':cloud.state['checked_at']=(NOW+timedelta(seconds=6)).isoformat()
    elif damage in{'blocked','latched'}:cloud.state[damage]=True
    elif damage=='expired':cloud.state['launch_lease_expires_at']=NOW.isoformat()
    elif damage=='credit-low':cloud.credit='5'
    elif damage=='missing-credit':cloud.credit=None
    else:cloud.state['guard_id']='other-guard'
    with pytest.raises(ValueError):launch(cloud)
    assert not any(k in{'lock','create-group','launch'}for k,_ in cloud.calls)


@pytest.mark.parametrize('field,value',[
 ('image_id','ami-OTHER'),('instance_type','p5.48xlarge'),('root_gib',201),('root_gib',True),
 ('minutes',46),('minutes',True),('budget_usd','7'),('budget_usd','1'),('budget_usd','NaN'),
 ('script_sha256','0'*64),('script_b64','INVALID=not-base64'),('script_b64','A'*24001),('source_sha256','INVALID')])
def test_closed_requested_cost_image_disk_duration_and_source_bounds(field,value):
    cloud=Cloud();req=request();req[field]=value
    with pytest.raises(ValueError):launch(cloud,req)
    assert not any(k in{'lock','create-group','launch'}for k,_ in cloud.calls)


def test_monitor_must_cover_whole_new_session_and_credit_covers_reserved_budget():
    cloud=Cloud();cfg=config();cfg['monitor_deadline_at']=(NOW+timedelta(minutes=44)).isoformat()
    with pytest.raises(ValueError,match='MONITOR_EXPIRES_BEFORE_SESSION'):launch(cloud,cfg=cfg)
    cloud.credit='10.99'
    with pytest.raises(ValueError,match='SESSION_BOUND_INVALID'):launch(cloud)
    assert not any(k=='launch'for k,_ in cloud.calls)


@pytest.mark.parametrize('kind',['active','disk','lock'])
def test_active_uncleaned_or_atomic_launch_lock_refuses_another_session(kind):
    cloud=Cloud()
    if kind=='active':cloud.instances=[instance()]
    elif kind=='disk':cloud.volumes=[{'VolumeId':'vol-EXAMPLE'}]
    else:cloud.lock='EXAMPLE_previous_unknown_launch'
    with pytest.raises(ValueError):launch(cloud)
    assert not any(k in{'create-group','launch'}for k,_ in cloud.calls)


def test_unknown_launch_is_not_retried_or_reported_as_no_resource_and_lock_is_kept():
    cloud=Cloud();cloud.launch_error=True
    with pytest.raises(RuntimeError,match='REQUERY_JOB'):launch(cloud)
    assert cloud.lock is not None and sum(k=='launch'for k,_ in cloud.calls)==1
    with pytest.raises(ValueError,match='LAUNCH_LOCKED_OR_UNVERIFIED'):launch(cloud)
    assert sum(k=='launch'for k,_ in cloud.calls)==1


def test_commands_are_owned_bounded_and_do_not_outlive_session():
    cloud=Cloud();item=instance();item['InstanceId']='i-0123456789abcdef0';cloud.instances=[item]
    script='echo EXAMPLE-owned-command';req={'action':'command','instance_id':item['InstanceId'],
        'seconds':30,'script':script,'script_sha256':hashlib.sha256(script.encode()).hexdigest()}
    result=runner.dispatch(req,config(),INFRA,factory=cloud.factory,now=NOW)
    assert result['instance_id']==item['InstanceId']and cloud.calls[-1][1]['DocumentName']=='AWS-RunShellScript'
    for changed in(dict(seconds=601),dict(script_sha256='0'*64),dict(seconds=True)):
        with pytest.raises(ValueError):runner.dispatch(req|changed,config(),INFRA,factory=cloud.factory,now=NOW)
    item['Tags']=resource_tags(**{guard.DEADLINE:(NOW+timedelta(seconds=20)).isoformat()})
    with pytest.raises(ValueError):runner.dispatch(req,config(),INFRA,factory=cloud.factory,now=NOW)


def test_cleanup_remains_allowed_after_credit_gate_closes_but_never_for_foreign_instance():
    cloud=Cloud();item=instance();item['InstanceId']='i-0123456789abcdef0';cloud.instances=[item];cloud.state['blocked']=True
    req={'action':'terminate','instance_id':item['InstanceId']}
    assert runner.dispatch(req,config(),INFRA,factory=cloud.factory,now=NOW)['termination_requested']==item['InstanceId']
    item['Tags']=resource_tags(**{guard.TAG:'other-guard'})
    with pytest.raises(ValueError,match='OUT_OF_SCOPE'):runner.dispatch(req,config(),INFRA,factory=cloud.factory,now=NOW)
    assert sum(k=='terminate'for k,_ in cloud.calls)==1


def test_execution_role_cannot_overwrite_the_credit_state_or_delete_launch_lock():
    policy=deploy.execution_policy(config(),INFRA)
    writes=[s for s in policy['Statement']if s.get('Effect')=='Allow'and s['Action']=='ssm:PutParameter']
    assert len(writes)==1 and writes[0]['Resource'].endswith('/launch-lock')
    assert 'ssm:DeleteParameter'not in json.dumps(policy)
