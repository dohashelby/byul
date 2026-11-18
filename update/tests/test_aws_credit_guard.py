"""Bounded owned-resource protection; no AWS credentials or network calls."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from tools import aws_credit_guard as g
from tools import aws_credit_guard_deploy as deploy

NOW=datetime(2026,11,17,12,tzinfo=timezone.utc)
def config():
    return {'account_id':'123456789012','region':'ap-northeast-2','guard_id':'byul-credit-example',
        'execution_role':'byul-credit-example-execution','state_parameter':'/byul/credit-guard/example/state',
        'reserve_usd':'5','maximum_session_usd':'6','maximum_session_minutes':45,'lease_seconds':180,
        'hourly_upper_usd':{'g6.12xlarge':'6.50','t3.micro':'0.10'},'fixed_session_reserve_usd':'0.75',
        'monitor_deadline_at':(NOW+timedelta(hours=24)).isoformat()}
def resource_tags(**extras):
    values={g.TAG:config()['guard_id'],g.EPHEMERAL:'true',g.DEADLINE:(NOW+timedelta(minutes=20)).isoformat(),g.BUDGET:'6'}
    values.update(extras);return [{'Key':k,'Value':v}for k,v in values.items()]
def instance():
    return {'InstanceId':'i-EXAMPLE-owned','InstanceType':'g6.12xlarge','State':{'Name':'running'},
        'LaunchTime':NOW-timedelta(minutes=1),'Tags':resource_tags(),
        'BlockDeviceMappings':[{'Ebs':{'DeleteOnTermination':True}}]}


class Cloud:
    def __init__(self):
        self.credit=Decimal('12.23');self.credit_error=False;self.instances=[];self.volumes=[];self.groups=[]
        self.calls=[];self.policies=[];self.state=None;self.sg_error=False
    def factory(self,*args):return self
    def list_role_policies(self,**kw):return {'PolicyNames':self.policies}
    def get_account_plan_state(self):
        if self.credit_error:raise OSError('EXAMPLE_PRIVATE_ERROR')
        return {'accountId':config()['account_id'],'accountPlanStatus':'ACTIVE',
            'accountPlanRemainingCredits':{'amount':self.credit,'unit':'USD'}}
    def describe_instances(self,**kw):return {'Reservations':[{'Instances':self.instances}]}
    def describe_volumes(self,**kw):return {'Volumes':self.volumes}
    def describe_security_groups(self,**kw):return {'SecurityGroups':self.groups}
    def put_role_policy(self,**kw):self.calls.append(('policy',kw))
    def put_parameter(self,**kw):self.state=json.loads(kw['Value']);self.calls.append(('state',kw))
    def terminate_instances(self,**kw):self.calls.append(('terminate',kw))
    def delete_volume(self,**kw):self.calls.append(('delete-volume',kw))
    def delete_security_group(self,**kw):
        if self.sg_error:raise OSError('EXAMPLE_PRIVATE_ERROR')
        self.calls.append(('delete-group',kw))
    def disable_rule(self,**kw):self.calls.append(('disable-monitor',kw))


def run(cloud):return g.run(config(),factory=cloud.factory,now=NOW)

def test_healthy_balance_issues_only_short_iam_launch_lease_and_current_state():
    cloud=Cloud();result=run(cloud)
    assert not result['blocked']and result['credit_usd']=='12.23'
    policy=json.loads(cloud.calls[0][1]['PolicyDocument'])['Statement'][0]
    assert policy['Effect']=='Deny'and policy['Condition']['DateGreaterThan']['aws:CurrentTime']==(NOW+timedelta(seconds=180)).isoformat()
    assert result['root_user_restricted']is result['all_account_spending_restricted']is False
    assert not any(c[0]in{'terminate','disable-monitor'}for c in cloud.calls)
    assert cloud.calls[-1][1]['Tier']=='Standard'


@pytest.mark.parametrize('credit',[0,4.99,5,'NaN','-1',True])
def test_low_invalid_or_missing_credit_latches_before_termination_and_self_disables_when_empty(credit):
    cloud=Cloud();cloud.credit=credit
    result=run(cloud)
    assert result['blocked']and result['latched']and result['monitor_disabled_after_latch']
    assert cloud.calls[0][1]['PolicyName']==g.LATCH
    assert any(c[0]=='disable-monitor'for c in cloud.calls)
    assert g.timestamp(result['launch_lease_expires_at'])<NOW


def test_lookup_failure_is_closed_and_exception_text_never_enters_public_state():
    cloud=Cloud();cloud.credit_error=True;cloud.instances=[instance()]
    result=run(cloud)
    assert result['blocked']and result['termination_requests']==[{'instance_id':'i-EXAMPLE-owned','reason':'CREDIT_GATE_BLOCKED'}]
    assert cloud.calls[0][0]=='policy'and cloud.calls[0][1]['PolicyName']==g.LATCH
    assert 'EXAMPLE_PRIVATE'not in json.dumps(result)and 'monitor_disabled_after_latch'not in result


def test_latch_is_not_cleared_when_credit_later_recovers():
    cloud=Cloud();cloud.policies=[g.LATCH];result=run(cloud)
    assert result['blocked']and result['latched']


@pytest.mark.parametrize('damage,reason',[
 ('deadline','SESSION_DEADLINE_REACHED'),('unknown-type','SESSION_METADATA_UNVERIFIED'),
 ('bad-budget','SESSION_METADATA_UNVERIFIED'),('missing-tag','SESSION_METADATA_UNVERIFIED'),
 ('disk-retained','SESSION_DISK_DELETE_ON_TERMINATION_UNVERIFIED'),('empty-disks','SESSION_DISK_DELETE_ON_TERMINATION_UNVERIFIED')])
def test_owned_deadline_or_configuration_gaps_stop_only_owned_instances(damage,reason):
    cloud=Cloud();item=instance()
    if damage=='deadline':item['Tags']=resource_tags(**{g.DEADLINE:NOW.isoformat()})
    elif damage=='unknown-type':item['InstanceType']='unknown.large'
    elif damage=='bad-budget':item['Tags']=resource_tags(**{g.BUDGET:'999'})
    elif damage=='missing-tag':item['Tags']=[v for v in item['Tags']if v['Key']!=g.DEADLINE]
    elif damage=='disk-retained':item['BlockDeviceMappings'][0]['Ebs']['DeleteOnTermination']=False
    else:item['BlockDeviceMappings']=[]
    cloud.instances=[item];result=run(cloud)
    assert result['termination_requests'][0]['reason']==reason


def test_deadline_cost_and_aggregate_reservations_do_not_depend_on_bill_refresh():
    cloud=Cloud();first=instance();second=deepcopy(first);second['InstanceId']='i-EXAMPLE-second'
    cloud.instances=[first,second];result=run(cloud)
    assert 'OWNED_COMMITMENTS_EXCEED_AVAILABLE_CREDIT'in result['diagnostic_codes']
    assert len(result['termination_requests'])==2
    item=instance();item['LaunchTime']=NOW-timedelta(minutes=40);item['Tags']=resource_tags(**{g.BUDGET:'5',g.DEADLINE:(NOW+timedelta(minutes=1)).isoformat()})
    assert g.instance_reason(item,config(),NOW)=='SESSION_COST_ENVELOPE_REACHED'


def test_never_deletes_attached_untagged_non_ephemeral_or_not_yet_expired_volumes():
    cloud=Cloud();expired=resource_tags(**{g.DEADLINE:(NOW-timedelta(seconds=1)).isoformat()})
    cloud.volumes=[{'VolumeId':'vol-EXAMPLE-expired','State':'available','Attachments':[],'Tags':expired},
      {'VolumeId':'vol-EXAMPLE-attached','State':'in-use','Attachments':[{}],'Tags':expired},
      {'VolumeId':'vol-EXAMPLE-user','State':'available','Attachments':[],'Tags':[]},
      {'VolumeId':'vol-EXAMPLE-preparing','State':'available','Attachments':[],'Tags':resource_tags()},
      {'VolumeId':'vol-EXAMPLE-retained','State':'available','Attachments':[],'Tags':[v for v in expired if v['Key']!=g.EPHEMERAL]}]
    cloud.groups=[{'GroupId':'sg-EXAMPLE-default','GroupName':'default','Tags':expired},
        {'GroupId':'sg-EXAMPLE-preparing','GroupName':'new','Tags':resource_tags()}]
    result=run(cloud)
    assert result['volume_deletions']==['vol-EXAMPLE-expired']and not result['security_group_deletions']


def test_inventory_cannot_trick_cleanup_into_other_guard_scope():
    cloud=Cloud();cloud.credit=0;other=instance();other['Tags']=resource_tags(**{g.TAG:'other-guard'})
    cloud.instances=[other];result=run(cloud)
    assert not result['termination_requests']


def test_pagination_keeps_iam_latch_and_ec2_ownership_boundaries():
    class API:
        def list_role_policies(self,**kwargs):
            return {'PolicyNames':[g.LATCH]}if kwargs.get('Marker')=='second'else {'PolicyNames':['base'],'IsTruncated':True,'Marker':'second'}
    assert g.page(API(),'list_role_policies','PolicyNames',RoleName='EXAMPLE')==['base',g.LATCH]


def test_execution_permissions_are_owned_region_and_image_limited_without_root_claim():
    infra={'image_ids':['ami-EXAMPLE'],'subnet_id':'subnet-EXAMPLE','vpc_id':'vpc-EXAMPLE',
        'instance_role_arn':'arn:aws:iam::123456789012:role/EXAMPLE','instance_profile_arn':'arn:aws:iam::123456789012:instance-profile/EXAMPLE'}
    policy=deploy.execution_policy(config(),infra);text=json.dumps(policy)
    assert 'AdministratorAccess'not in text and 'iam:PutRolePolicy'not in text
    assert 'aws:RequestTag/byul:credit-guard'in text and 'ec2:VolumeSize'in text
    watcher=json.dumps(deploy.watcher_policy(config()))
    assert 'aws:ResourceTag/byul:ephemeral'in watcher and 'events:DisableRule'in watcher


def test_monitor_itself_expires_and_latches_without_an_unbounded_polling_cost():
    cloud=Cloud();cfg=config();cfg['monitor_deadline_at']=NOW.isoformat()
    result=g.run(cfg,factory=cloud.factory,now=NOW)
    assert result['blocked']and result['monitor_disabled_after_latch']
    assert 'MONITOR_DEADLINE_REACHED'in result['diagnostic_codes']


def test_failed_iam_renewal_never_produces_a_healthy_state_or_cleanup_claim():
    class Denied(Cloud):
        def put_role_policy(self,**kwargs):raise PermissionError('unavailable')
    cloud=Denied();cloud.instances=[instance()]
    with pytest.raises(PermissionError):run(cloud)
    assert cloud.state is None and not any(c[0]=='terminate_instances'for c in cloud.calls)


@pytest.mark.parametrize('target_result, installed', [
    ({'FailedEntryCount': 0, 'FailedEntries': []}, True),
    ({'FailedEntryCount': 1, 'FailedEntries': [{'TargetId': 'OwnedCreditGuard', 'ErrorCode': 'InternalException'}]}, False),
    ({}, False),
])
def test_deploy_requires_target_registration_before_initial_invocation(tmp_path, target_result, installed):
    cfg = config()
    cfg['monitor_deadline_at'] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    arn = f"arn:aws:lambda:{cfg['region']}:{cfg['account_id']}:function:{cfg['guard_id']}"
    role = f"arn:aws:iam::{cfg['account_id']}:role/EXAMPLE"
    infra = {'image_ids': ['ami-EXAMPLE'], 'subnet_id': 'subnet-EXAMPLE', 'vpc_id': 'vpc-EXAMPLE',
             'instance_role_arn': role, 'instance_profile_arn': role.replace(':role/', ':instance-profile/')}
    responses = {
        ('sts', 'get-caller-identity'): {'Account': cfg['account_id']},
        ('freetier', 'get-account-plan-state'): Cloud().get_account_plan_state(),
        ('iam', 'get-role'): None,
        ('iam', 'create-role'): {'Role': {'Arn': role}},
        ('logs', 'describe-log-groups'): {'logGroups': []},
        ('lambda', 'get-function'): None,
        ('lambda', 'create-function'): {'FunctionArn': arn},
        ('events', 'describe-rule'): None,
        ('events', 'put-rule'): {'RuleArn': f"arn:aws:events:{cfg['region']}:{cfg['account_id']}:rule/{cfg['guard_id']}"},
        ('events', 'put-targets'): target_result,
    }

    def call(service, operation, *args, **kwargs):
        if (service, operation) == ('lambda', 'invoke'):
            Path(args[-1]).write_text(json.dumps({
                'schema': g.SCHEMA, 'blocked': False,
                'source_sha256': hashlib.sha256(Path(g.__file__).read_bytes()).hexdigest(),
            }))
        return responses.get((service, operation), {})

    api = Mock(output=tmp_path)
    api.call.side_effect = call
    api.document.side_effect = lambda name, value: 'file://' + str(tmp_path / name)
    if installed:
        assert deploy.deploy(api, cfg, infra)['installed_schedule'] == 'rate(1 minute)'
        assert (tmp_path / 'deployment.json').is_file()
    else:
        with pytest.raises(RuntimeError, match='CREDIT_GUARD_SCHEDULE_TARGET_UNVERIFIED'):
            deploy.deploy(api, cfg, infra)
        assert not (tmp_path / 'deployment.json').exists()
    operations = [call.args[:2] for call in api.call.call_args_list]
    assert ('events', 'put-targets') in operations
    assert (('events', 'enable-rule') in operations) is installed
    assert (('lambda', 'invoke') in operations) is installed
    rule_args = api.call.call_args_list[operations.index(('events', 'put-rule'))].args
    assert rule_args[rule_args.index('--state') + 1] == 'DISABLED'
    if installed:
        assert (operations.index(('events', 'put-targets')) < operations.index(('events', 'enable-rule'))
                < operations.index(('lambda', 'invoke')))
