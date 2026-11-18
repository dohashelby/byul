"""Owned-lab credit cutoff. Runtime SDK is supplied by AWS Lambda, not guard.

The account's root user is outside this IAM gate. Resource cleanup is confined
to the exact guard tag and explicitly ephemeral newly created lab volumes.
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
import hashlib,json,os,re
from pathlib import Path

SCHEMA='byul-owned-lab-credit-guard/1'
TAG='byul:credit-guard'
EPHEMERAL='byul:ephemeral'
DEADLINE='byul:deadline'
BUDGET='byul:budget-usd'
PAID_ACTIONS=['ec2:RunInstances','ec2:StartInstances','ec2:CreateVolume',
 'ec2:CreateSnapshot','ec2:CreateSnapshots','ec2:CreateSecurityGroup','ssm:SendCommand']
LEASE='CreditGuardLease'
LATCH='CreditGuardLatched'


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'))


def timestamp(value):
    if type(value)is not str:raise ValueError('CREDIT_GUARD_TIMESTAMP_INVALID')
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('CREDIT_GUARD_TIMESTAMP_INVALID')
    return t.astimezone(timezone.utc)


def amount(value):
    if type(value)not in(int,float,str,Decimal)or type(value)is bool:raise ValueError('CREDIT_GUARD_AMOUNT_INVALID')
    try:n=Decimal(str(value))
    except InvalidOperation:raise ValueError('CREDIT_GUARD_AMOUNT_INVALID')from None
    if not n.is_finite()or n<0:raise ValueError('CREDIT_GUARD_AMOUNT_INVALID')
    return n


def validate_config(cfg):
    required={'account_id','region','guard_id','execution_role','state_parameter','reserve_usd',
        'maximum_session_usd','maximum_session_minutes','lease_seconds','hourly_upper_usd','fixed_session_reserve_usd','monitor_deadline_at'}
    if(type(cfg)is not dict or set(cfg)!=required
            or any(type(cfg[k])is not str for k in('account_id','region','guard_id','execution_role','state_parameter'))
            or re.fullmatch('[0-9]{12}',cfg['account_id'])is None
            or re.fullmatch('[a-z]{2}-[a-z]+-[0-9]',cfg['region'])is None
            or re.fullmatch('[a-z0-9-]{8,64}',cfg['guard_id'])is None
            or re.fullmatch('[A-Za-z0-9+=,.@_-]{1,64}',cfg['execution_role'])is None
            or re.fullmatch('/byul/credit-guard/[a-z0-9-]+/state',cfg['state_parameter'])is None
            or type(cfg['maximum_session_minutes'])is not int or not 1<=cfg['maximum_session_minutes']<=120
            or type(cfg['lease_seconds'])is not int or not 120<=cfg['lease_seconds']<=180
            or type(cfg['hourly_upper_usd'])is not dict or not cfg['hourly_upper_usd']):
        raise ValueError('CREDIT_GUARD_CONFIG_INVALID')
    if amount(cfg['reserve_usd'])<5 or not 0<amount(cfg['maximum_session_usd'])<=6:raise ValueError('CREDIT_GUARD_BUDGET_INVALID')
    if not 0<=amount(cfg['fixed_session_reserve_usd'])<amount(cfg['maximum_session_usd']):raise ValueError('CREDIT_GUARD_BUDGET_INVALID')
    for kind,rate in cfg['hourly_upper_usd'].items():
        if re.fullmatch(r'[a-z0-9]+\.[a-z0-9]+',kind)is None or not 0<amount(rate)<=100:
            raise ValueError('CREDIT_GUARD_RATE_INVALID')
    timestamp(cfg['monitor_deadline_at'])
    return cfg


def credit_balance(plan,account):
    if(type(plan)is not dict or plan.get('accountId')!=account or plan.get('accountPlanStatus')!='ACTIVE'
            or plan.get('accountPlanRemainingCredits',{}).get('unit')!='USD'):
        raise ValueError('CREDIT_GUARD_CREDIT_UNVERIFIED')
    value=amount(plan['accountPlanRemainingCredits']['amount'])
    # Free Tier remaining credit is conservative if separate enterprise or
    # promotional credits exist. A missing balance never grants a launch.
    return value


def deny_document(*,expires=None):
    statement={'Effect':'Deny','Action':PAID_ACTIONS,'Resource':'*'}
    if expires is not None:statement['Condition']={'DateGreaterThan':{'aws:CurrentTime':expires.isoformat()}}
    return {'Version':'2012-10-17','Statement':[statement]}


def tags(resource):
    values=resource.get('Tags',[])
    if type(values)is not list:return {}
    return {v['Key']:v['Value']for v in values if type(v)is dict and type(v.get('Key'))is str and type(v.get('Value'))is str}


def instance_reason(instance,cfg,now):
    """Budgets are bounded by AWS birth time, fixed rates and closed tags."""
    values=tags(instance)
    if values.get(TAG)!=cfg['guard_id']:return 'OUTSIDE_GUARD_SCOPE'
    try:
        if values.get(EPHEMERAL)!='true':raise ValueError
        deadline=timestamp(values[DEADLINE]);born=instance['LaunchTime']
        if type(born)is str:born=timestamp(born)
        if not isinstance(born,datetime)or born.tzinfo is None or born>now+timedelta(seconds=60):raise ValueError
        budget=amount(values[BUDGET]);rate=amount(cfg['hourly_upper_usd'][instance['InstanceType']])
        if not 0<budget<=amount(cfg['maximum_session_usd']):raise ValueError
        if not born<=deadline<=born+timedelta(minutes=cfg['maximum_session_minutes']):raise ValueError
        if now>=deadline:return 'SESSION_DEADLINE_REACHED'
        elapsed=amount(max(0,(now-born).total_seconds()))/Decimal(3600)
        if elapsed*rate+amount(cfg['fixed_session_reserve_usd'])>=budget:return 'SESSION_COST_ENVELOPE_REACHED'
        mappings=instance.get('BlockDeviceMappings',[])
        if(instance['State']['Name']!='pending'and not mappings
                or any(m.get('Ebs',{}).get('DeleteOnTermination')is not True for m in mappings)):
            return 'SESSION_DISK_DELETE_ON_TERMINATION_UNVERIFIED'
    except(KeyError,TypeError,ValueError,InvalidOperation):return 'SESSION_METADATA_UNVERIFIED'
    return None


def client(service,region):
    import boto3  # AWS Lambda includes this SDK; no update package dependency.
    from botocore.config import Config
    return boto3.client(service,region_name=region,config=Config(connect_timeout=3,read_timeout=5,retries={'max_attempts':1}))


def page(api,operation,key,**kwargs):
    result=[];token=None
    for _ in range(20):
        marker='Marker'if operation=='list_role_policies'else'NextToken'
        value=getattr(api,operation)(**kwargs,**({marker:token}if token else{}))
        result.extend(value.get(key,[]));token=value.get(marker)
        if value.get('IsTruncated')is True and not token:raise ValueError('CREDIT_GUARD_INVENTORY_INCOMPLETE')
        if not token or value.get('IsTruncated')is False:return result
    raise ValueError('CREDIT_GUARD_INVENTORY_BOUND')


def run(cfg,*,factory=client,now=None):
    validate_config(cfg);now=now or datetime.now(timezone.utc)
    iam=factory('iam','us-east-1');ssm=factory('ssm',cfg['region']);ec2=factory('ec2',cfg['region'])
    result={'schema':SCHEMA,'checked_at':now.isoformat(),'guard_id':cfg['guard_id'],
        'credit_usd':None,'reserve_usd':str(amount(cfg['reserve_usd'])),'blocked':False,
        'latched':False,'diagnostic_codes':[],'termination_requests':[],'volume_deletions':[],
        'security_group_deletions':[],'root_user_restricted':False,'all_account_spending_restricted':False,
        'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    # A previous stop is latched. Healthy later credit does not silently undo it.
    policies=page(iam,'list_role_policies','PolicyNames',RoleName=cfg['execution_role'])
    result['latched']=LATCH in policies
    if now>=timestamp(cfg['monitor_deadline_at']):result['diagnostic_codes'].append('MONITOR_DEADLINE_REACHED')
    try:
        credit=credit_balance(factory('freetier','us-east-1').get_account_plan_state(),cfg['account_id'])
        result['credit_usd']=str(credit)
        if credit<=amount(cfg['reserve_usd']):result['diagnostic_codes'].append('CREDIT_RESERVE_REACHED')
    except Exception:
        credit=None;result['diagnostic_codes'].append('CREDIT_LOOKUP_UNVERIFIED')
    filters=[{'Name':'tag:'+TAG,'Values':[cfg['guard_id']]}]
    try:
        reservations=page(ec2,'describe_instances','Reservations',Filters=filters)
        active=[i for r in reservations for i in r['Instances']if i['State']['Name']in{'pending','running','stopping','stopped'}]
    except Exception:
        active=[];result['diagnostic_codes'].append('OWNED_INSTANCE_INVENTORY_UNVERIFIED')
    if credit is not None:
        try:
            committed=sum((amount(tags(i)[BUDGET])for i in active),Decimal(0))
            if committed>max(Decimal(0),credit-amount(cfg['reserve_usd'])):
                result['diagnostic_codes'].append('OWNED_COMMITMENTS_EXCEED_AVAILABLE_CREDIT')
        except Exception:result['diagnostic_codes'].append('OWNED_BUDGET_METADATA_UNVERIFIED')
    reasons={i['InstanceId']:instance_reason(i,cfg,now)for i in active}
    # A malformed owned session latches the gate, independently of billing lag.
    if any(reason in{'SESSION_METADATA_UNVERIFIED','SESSION_DISK_DELETE_ON_TERMINATION_UNVERIFIED'}for reason in reasons.values()):
        result['diagnostic_codes'].append('OWNED_SESSION_CONFIGURATION_UNVERIFIED')
    result['blocked']=bool(result['latched']or result['diagnostic_codes'])
    if result['blocked']:
        iam.put_role_policy(RoleName=cfg['execution_role'],PolicyName=LATCH,PolicyDocument=canonical(deny_document()))
        result['latched']=True
    expires=now-timedelta(seconds=1)if result['blocked']else now+timedelta(seconds=cfg['lease_seconds'])
    # An expired IAM lease denies launches even if this Lambda/schedule stops.
    iam.put_role_policy(RoleName=cfg['execution_role'],PolicyName=LEASE,PolicyDocument=canonical(deny_document(expires=expires)))
    result['launch_lease_expires_at']=expires.isoformat()
    for instance in active:
        iid=instance['InstanceId'];reason=reasons[iid]
        if tags(instance).get(TAG)!=cfg['guard_id']:continue
        if result['blocked']or reason is not None:
            ec2.terminate_instances(InstanceIds=[iid])
            result['termination_requests'].append({'instance_id':iid,'reason':reason or 'CREDIT_GATE_BLOCKED'})
    # The Lambda never deletes an untagged, non-ephemeral or attached disk.
    volumes=page(ec2,'describe_volumes','Volumes',Filters=filters)
    pending_cleanup=bool(active)
    for volume in volumes:
        try:expired=timestamp(tags(volume)[DEADLINE])<=now
        except(ValueError,KeyError):expired=False
        if(tags(volume).get(TAG)==cfg['guard_id']and tags(volume).get(EPHEMERAL)=='true'
                and(result['blocked']or expired)and volume['State']=='available'and not volume.get('Attachments')):
            ec2.delete_volume(VolumeId=volume['VolumeId']);result['volume_deletions'].append(volume['VolumeId'])
        elif tags(volume).get(TAG)==cfg['guard_id']:pending_cleanup=True
    groups=page(ec2,'describe_security_groups','SecurityGroups',Filters=filters)
    for group in groups:
        if tags(group).get(TAG)!=cfg['guard_id']or tags(group).get(EPHEMERAL)!='true'or group.get('GroupName')=='default':continue
        try:expired=timestamp(tags(group)[DEADLINE])<=now
        except(ValueError,KeyError):expired=False
        if not(result['blocked']or expired):continue
        try:
            ec2.delete_security_group(GroupId=group['GroupId']);result['security_group_deletions'].append(group['GroupId'])
        except Exception:
            pending_cleanup=True;result['diagnostic_codes'].append('OWNED_SECURITY_GROUP_CLEANUP_PENDING')
    if result['blocked']and not pending_cleanup:
        factory('events',cfg['region']).disable_rule(Name=cfg['guard_id'])
        result['monitor_disabled_after_latch']=True
    # Cleanup gaps are retained but do not rewrite successful health or unblock.
    ssm.put_parameter(Name=cfg['state_parameter'],Type='String',Tier='Standard',Overwrite=True,Value=canonical(result))
    return result


def handler(event,context):
    # Events cannot provide balances, IDs, thresholds or a force-unblock flag.
    cfg=json.loads(os.environ['CREDIT_GUARD_CONFIG'])
    return run(cfg)
