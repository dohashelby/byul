"""Run an owned lab through its restricted role; never root EC2/SSM execution.

The root/operator deploys and invokes this Lambda. Actual EC2 and SSM calls
use the existing guarded execution role, including its expiring IAM Deny.
Only stdlib is needed outside the Lambda-provided Boto3 runtime.
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
import argparse,base64,hashlib,json,os,re,zipfile
from pathlib import Path
from decimal import Decimal
from tools import aws_credit_guard as guard

SCHEMA='byul-credit-guarded-owned-session/1'
JOB='byul:job'
STATES=['pending','running','stopping','stopped']


def _closed(value,keys):
    if type(value)is not dict or set(value)!=set(keys):raise ValueError('OWNED_LAB_REQUEST_INVALID')


def _hash(value):return type(value)is str and re.fullmatch('[0-9a-f]{64}',value)is not None


def _job(value):
    if type(value)is not str or re.fullmatch('[0-9a-f]{32}',value)is None:raise ValueError('OWNED_LAB_JOB_INVALID')
    return value


def _healthy(cfg,factory,now):
    guard.validate_config(cfg)
    value=factory('ssm',cfg['region']).get_parameter(Name=cfg['state_parameter'])['Parameter']['Value']
    state=json.loads(value)
    if(state.get('schema')!=guard.SCHEMA or state.get('guard_id')!=cfg['guard_id']
            or state.get('blocked')is not False or state.get('latched')is not False
            or not now-timedelta(seconds=90)<=guard.timestamp(state['checked_at'])<=now+timedelta(seconds=5)
            or guard.timestamp(state['launch_lease_expires_at'])<=now
            or guard.timestamp(cfg['monitor_deadline_at'])<=now):raise ValueError('OWNED_LAB_CREDIT_GATE_CLOSED')
    credit=guard.credit_balance(factory('freetier','us-east-1').get_account_plan_state(),cfg['account_id'])
    if credit<=guard.amount(cfg['reserve_usd']):raise ValueError('OWNED_LAB_CREDIT_RESERVE_REACHED')
    return credit


def _identity(cfg,factory):
    identity=factory('sts',cfg['region']).get_caller_identity()['Arn']
    prefix=f'arn:aws:sts::{cfg["account_id"]}:assumed-role/'+cfg['execution_role']+'/'
    if type(identity)is not str or not identity.startswith(prefix)or len(identity)<=len(prefix):raise ValueError('OWNED_LAB_EXECUTION_ROLE_UNVERIFIED')
    return identity


def _owned(ec2,cfg,instance_id):
    if type(instance_id)is not str or re.fullmatch(r'i-[0-9a-f]{8,17}',instance_id)is None:raise ValueError('OWNED_LAB_INSTANCE_INVALID')
    reservations=ec2.describe_instances(InstanceIds=[instance_id])['Reservations']
    rows=[i for r in reservations for i in r['Instances']]
    if(len(rows)!=1 or guard.tags(rows[0]).get(guard.TAG)!=cfg['guard_id']
            or guard.tags(rows[0]).get(guard.EPHEMERAL)!='true'):raise ValueError('OWNED_LAB_INSTANCE_OUT_OF_SCOPE')
    return rows[0]


def launch(request,cfg,infra,*,factory=guard.client,now=None):
    now=now or datetime.now(timezone.utc)
    _closed(request,{'action','job','image_id','instance_type','root_gib','minutes','budget_usd','source_sha256','script_b64','script_sha256'})
    if request['action']!='launch':raise ValueError('OWNED_LAB_REQUEST_INVALID')
    identity=_identity(cfg,factory);job=_job(request['job']);credit=_healthy(cfg,factory,now)
    budget=guard.amount(request['budget_usd']);minutes=request['minutes'];kind=request['instance_type'];gib=request['root_gib']
    if(type(minutes)is not int or not 1<=minutes<=cfg['maximum_session_minutes']
            or type(gib)is not int or not 8<=gib<=200 or request['image_id']not in infra['image_ids']
            or kind not in cfg['hourly_upper_usd']or not _hash(request['source_sha256'])
            or not _hash(request['script_sha256'])or not 0<budget<=guard.amount(cfg['maximum_session_usd'])
            or budget>credit-guard.amount(cfg['reserve_usd'])):raise ValueError('OWNED_LAB_SESSION_BOUND_INVALID')
    estimated=guard.amount(cfg['hourly_upper_usd'][kind])*Decimal(minutes)/Decimal(60)+guard.amount(cfg['fixed_session_reserve_usd'])
    if estimated>budget:raise ValueError('OWNED_LAB_COST_ENVELOPE_EXCEEDS_BUDGET')
    try:
        if type(request['script_b64'])is not str or len(request['script_b64'])>24000:raise ValueError
        script=base64.b64decode(request['script_b64'],validate=True)
        if(not 0<len(script)<=16000 or hashlib.sha256(script).hexdigest()!=request['script_sha256']
                or b'\0'in script):raise ValueError
        script=script.decode('utf-8')
    except(ValueError,TypeError,UnicodeError):raise ValueError('OWNED_LAB_SCRIPT_INVALID')from None
    ec2=factory('ec2',cfg['region']);filters=[{'Name':'tag:'+guard.TAG,'Values':[cfg['guard_id']]},
        {'Name':'instance-state-name','Values':STATES}]
    active=guard.page(ec2,'describe_instances','Reservations',Filters=filters)
    if any(r['Instances']for r in active):raise ValueError('OWNED_LAB_ACTIVE_SESSION_EXISTS')
    volumes=guard.page(ec2,'describe_volumes','Volumes',Filters=filters[:1])
    if volumes:raise ValueError('OWNED_LAB_UNCLEANED_VOLUMES_EXIST')
    deadline=now+timedelta(minutes=minutes)
    if deadline>guard.timestamp(cfg['monitor_deadline_at']):raise ValueError('OWNED_LAB_MONITOR_EXPIRES_BEFORE_SESSION')
    # Standard SSM PutParameter(Overwrite=False) is the atomic one-launch lock.
    # A timeout cannot start another session. Only the operator can clear this
    # exact lock after checking actual cleanup and settled remaining credit.
    lock=cfg['state_parameter'].removesuffix('/state')+'/launch-lock'
    try:
        factory('ssm',cfg['region']).put_parameter(Name=lock,Type='String',Tier='Standard',Overwrite=False,
            Value=guard.canonical({'schema':SCHEMA,'job':job,'deadline':deadline.isoformat(),
                'budget_usd':str(budget),'source_sha256':request['source_sha256'],'script_sha256':request['script_sha256']}))
    except Exception:raise ValueError('OWNED_LAB_SESSION_LAUNCH_LOCKED_OR_UNVERIFIED')from None
    tags=[{'Key':k,'Value':v}for k,v in {guard.TAG:cfg['guard_id'],guard.EPHEMERAL:'true',
        guard.DEADLINE:deadline.isoformat(),guard.BUDGET:str(budget),JOB:job,
        'byul:source':request['source_sha256'],'Name':cfg['guard_id']+'-'+job[:8]}.items()]
    # Scheduling precedes the user script. The node shutdown is independent of
    # this Lambda, the client process, credentials and the scheduled watcher.
    userdata=f'#!/bin/bash\nset -euo pipefail\n/sbin/shutdown -h +{minutes}\n'+script+'\n'
    group=ec2.create_security_group(GroupName=cfg['guard_id']+'-'+job[:8],
        Description='Owned guarded lab, no inbound access',VpcId=infra['vpc_id'],
        TagSpecifications=[{'ResourceType':'security-group','Tags':tags}])['GroupId']
    try:
        params={'ImageId':request['image_id'],'InstanceType':kind,'MinCount':1,'MaxCount':1,
            'SubnetId':infra['subnet_id'],'SecurityGroupIds':[group],
            'ClientToken':hashlib.sha256((cfg['guard_id']+job).encode()).hexdigest(),
            'InstanceInitiatedShutdownBehavior':'terminate','MetadataOptions':{'HttpTokens':'required','HttpEndpoint':'enabled','HttpPutResponseHopLimit':1},
            'IamInstanceProfile':{'Arn':infra['instance_profile_arn']},'UserData':userdata,
            'BlockDeviceMappings':[{'DeviceName':infra.get('root_device','/dev/sda1'),'Ebs':{
                'VolumeSize':gib,'VolumeType':'gp3','Encrypted':True,'DeleteOnTermination':True}}],
            'TagSpecifications':[{'ResourceType':resource,'Tags':tags}for resource in('instance','volume')]}
        if kind.startswith('t3.'):params['CreditSpecification']={'CpuCredits':'standard'}
        value=ec2.run_instances(**params)
    except Exception:
        # A timed-out response may already have launched the node. Do not retry
        # with a new token or claim absence; tagged resources remain monitored.
        raise RuntimeError('OWNED_LAB_LAUNCH_OUTCOME_UNVERIFIED_REQUERY_JOB')from None
    rows=value['Instances']
    if len(rows)!=1:raise RuntimeError('OWNED_LAB_LAUNCH_OUTCOME_UNVERIFIED_REQUERY_JOB')
    return {'schema':SCHEMA,'job':job,'instance_id':rows[0]['InstanceId'],'security_group_id':group,
        'deadline':deadline.isoformat(),'budget_usd':str(budget),'source_sha256':request['source_sha256'],
        'script_sha256':request['script_sha256'],'execution_role':identity,
        'credit_usd_at_launch':str(credit),'reserve_usd':str(guard.amount(cfg['reserve_usd']))}


def dispatch(request,cfg,infra,*,factory=guard.client,now=None):
    now=now or datetime.now(timezone.utc)
    if type(request)is not dict:raise ValueError('OWNED_LAB_REQUEST_INVALID')
    if request.get('action')=='launch':return launch(request,cfg,infra,factory=factory,now=now)
    _identity(cfg,factory)
    ec2=factory('ec2',cfg['region']);action=request.get('action')
    if action in{'status','terminate'}:
        _closed(request,{'action','instance_id'});instance=_owned(ec2,cfg,request['instance_id'])
        if action=='terminate':
            ec2.terminate_instances(InstanceIds=[instance['InstanceId']])
            return {'schema':SCHEMA,'termination_requested':instance['InstanceId']}
        return {'schema':SCHEMA,'instance_id':instance['InstanceId'],'state':instance['State']['Name'],
            'deadline':guard.tags(instance).get(guard.DEADLINE),'job':guard.tags(instance).get(JOB)}
    if action=='command':
        _closed(request,{'action','instance_id','script','script_sha256','seconds'});_healthy(cfg,factory,now)
        instance=_owned(ec2,cfg,request['instance_id']);seconds=request['seconds'];script=request['script']
        if(instance['State']['Name']!='running'or guard.instance_reason(instance,cfg,now)is not None
                or type(seconds)is not int or not 1<=seconds<=600
                or now+timedelta(seconds=seconds)>=guard.timestamp(guard.tags(instance)[guard.DEADLINE])
                or type(script)is not str or not 0<len(script.encode())<=16000 or '\x00'in script
                or hashlib.sha256(script.encode()).hexdigest()!=request['script_sha256']):raise ValueError('OWNED_LAB_COMMAND_BOUND_INVALID')
        value=factory('ssm',cfg['region']).send_command(InstanceIds=[instance['InstanceId']],DocumentName='AWS-RunShellScript',
            TimeoutSeconds=seconds,Parameters={'commands':[script],'executionTimeout':[str(seconds)]},
            CloudWatchOutputConfig={'CloudWatchOutputEnabled':False})
        return {'schema':SCHEMA,'instance_id':instance['InstanceId'],'command_id':value['Command']['CommandId'],
            'script_sha256':request['script_sha256']}
    if action=='command-result':
        _closed(request,{'action','instance_id','command_id'});_owned(ec2,cfg,request['instance_id'])
        if type(request['command_id'])is not str or re.fullmatch(r'[0-9a-f-]{36}',request['command_id'])is None:raise ValueError('OWNED_LAB_COMMAND_INVALID')
        value=factory('ssm',cfg['region']).get_command_invocation(CommandId=request['command_id'],InstanceId=request['instance_id'])
        return {key:value.get(key)for key in('Status','ResponseCode','StandardOutputContent','StandardErrorContent','ExecutionStartDateTime','ExecutionEndDateTime')}
    raise ValueError('OWNED_LAB_ACTION_INVALID')


def handler(event,context):
    settings=json.loads(os.environ['OWNED_LAB_CONFIG'])
    return dispatch(event,settings['guard'],settings['infrastructure'])


def deploy(api,settings):
    cfg=settings['guard'];guard.validate_config(cfg);name=cfg['guard_id']+'-runner'
    role=f'arn:aws:iam::{cfg["account_id"]}:role/'+cfg['execution_role']
    role_value=api.call('iam','get-role','--role-name',cfg['execution_role'])['Role']
    if not any(t['Key']==guard.TAG and t['Value']==cfg['guard_id']for t in role_value.get('Tags',[])):raise ValueError('Unowned execution role')
    old=api.call('lambda','get-function','--function-name',name,missing='ResourceNotFoundException')
    if old is not None and old.get('Tags',{}).get(guard.TAG)!=cfg['guard_id']:raise ValueError('Unowned runner')
    archive=api.output/'owned-runner.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED)as z:
        z.writestr('tools/__init__.py',b'')
        for file in(Path(__file__),Path(guard.__file__)):z.writestr('tools/'+file.name,file.read_bytes())
    env=api.document('owned-runner-environment.json',{'Variables':{'OWNED_LAB_CONFIG':guard.canonical(settings)}})
    if old is None:
        value=api.call('lambda','create-function','--function-name',name,'--runtime','python3.12','--role',role,
            '--handler','tools.aws_credit_guard_runner.handler','--timeout','50','--memory-size','128',
            '--zip-file','fileb://'+str(archive),'--environment',env,'--tags',json.dumps({guard.TAG:cfg['guard_id']}))
        api.call('lambda','wait','function-active-v2','--function-name',name)
    else:
        api.call('lambda','update-function-code','--function-name',name,'--zip-file','fileb://'+str(archive))
        api.call('lambda','wait','function-updated-v2','--function-name',name)
        value=api.call('lambda','update-function-configuration','--function-name',name,'--role',role,'--environment',env)
        api.call('lambda','wait','function-updated-v2','--function-name',name)
    # Suppress asynchronous retries; callers also never retry an unknown launch.
    api.call('lambda','put-function-event-invoke-config','--function-name',name,'--maximum-retry-attempts','0','--maximum-event-age-in-seconds','60')
    return {'schema':SCHEMA,'runner':name,'execution_role_arn':role,'function_arn':value['FunctionArn'],
        'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'gpu_launched':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in('config','aws-binary','profile','output'):parser.add_argument('--'+key,required=True)
    parser.add_argument('--deploy',action='store_true');parser.add_argument('--request',type=Path)
    args=parser.parse_args();settings=json.loads(Path(args.config).read_bytes())
    from tools.aws_credit_guard_deploy import CLI
    api=CLI(args.aws_binary,args.profile,settings['guard']['region'],args.output)
    if args.deploy:result=deploy(api,settings)
    else:
        if args.request is None:parser.error('--deploy or --request required')
        response=api.output/'owned-runner-response.json'
        invoke=api.call('lambda','invoke','--function-name',settings['guard']['guard_id']+'-runner',
            '--payload','fileb://'+str(args.request),str(response))
        result=json.loads(response.read_bytes())
        if invoke.get('FunctionError'):raise RuntimeError('OWNED_LAB_FUNCTION_FAILED: '+str(result.get('errorType'))+' '+str(result.get('errorMessage'))[:300])
    print(json.dumps(result))


if __name__=='__main__':main()
